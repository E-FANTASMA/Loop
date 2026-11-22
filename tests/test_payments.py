import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class PaymentHelperTests(unittest.TestCase):
    def setUp(self):
        self.old_values = {
            "SUPABASE_MODE": server.SUPABASE_MODE,
            "DB_PATH": server.DB_PATH,
            "UPLOADS_DIR": server.UPLOADS_DIR,
            "PAYSTACK_ENABLED": server.PAYSTACK_ENABLED,
            "PAYSTACK_SECRET_KEY": server.PAYSTACK_SECRET_KEY,
            "PAYSTACK_CALLBACK_URL": server.PAYSTACK_CALLBACK_URL,
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        server.SUPABASE_MODE = False
        server.DB_PATH = Path(self.temp_dir.name) / "loop.sqlite3"
        server.UPLOADS_DIR = Path(self.temp_dir.name) / "uploads"
        server.PAYSTACK_ENABLED = True
        server.PAYSTACK_SECRET_KEY = "sk_test_unit"
        server.PAYSTACK_CALLBACK_URL = "https://loop.example/"
        server.initialize_db()

    def tearDown(self):
        for name, value in self.old_values.items():
            setattr(server, name, value)
        self.temp_dir.cleanup()

    def create_pending_order(self, listing_status="payment_pending"):
        with server.connect_db() as db:
            buyer_id = db.execute("""
                INSERT INTO users (email, display_name, password_salt, password_hash, created_at)
                VALUES ('buyer@pau.edu.ng', 'Buyer', '', '', ?)
                RETURNING id
            """, (server.utc_now(),)).fetchone()["id"]
            seller_id = db.execute("""
                INSERT INTO users (email, display_name, password_salt, password_hash, created_at)
                VALUES ('seller@pau.edu.ng', 'Seller', '', '', ?)
                RETURNING id
            """, (server.utc_now(),)).fetchone()["id"]
            listing_id = db.execute("""
                INSERT INTO listings
                    (title, category, price, condition, location, seller_name, created_at, owner_id, status)
                VALUES ('Desk', 'Furniture', 20000, 'Good', 'Campus', 'Seller', ?, ?, ?)
                RETURNING id
            """, (server.utc_now(), seller_id, listing_status)).fetchone()["id"]
            order_id = db.execute("""
                INSERT INTO orders
                    (listing_id, buyer_id, seller_id, amount, status, created_at,
                     payment_reference, platform_fee, reservation_expires_at)
                VALUES (?, ?, ?, 20000, 'payment_pending', ?, 'loop_test_reference', 500, ?)
                RETURNING id
            """, (
                listing_id, buyer_id, seller_id, server.utc_now(),
                "2099-01-01T00:00:00+00:00",
            )).fetchone()["id"]
            if listing_status == "payment_pending":
                db.execute("""
                    UPDATE listings SET payment_pending_order_id = ?,
                        reservation_expires_at = '2099-01-01T00:00:00+00:00'
                    WHERE id = ?
                """, (order_id, listing_id))
        return order_id, listing_id

    def test_platform_fee_tiers(self):
        self.assertEqual(server.platform_fee_for(0), 0)
        self.assertEqual(server.platform_fee_for(10000), 200)
        self.assertEqual(server.platform_fee_for(10001), 500)
        self.assertEqual(server.platform_fee_for(29999), 500)
        self.assertEqual(server.platform_fee_for(30000), 1000)

    def test_split_uses_kobo_and_seller_bears_processor_fee(self):
        payload = server.paystack_split_payload(
            "buyer@pau.edu.ng", 20000, 500, "loop_reference", "ACCT_test"
        )
        self.assertEqual(payload["amount"], 2_000_000)
        self.assertEqual(payload["transaction_charge"], 50_000)
        self.assertEqual(payload["bearer"], "subaccount")
        self.assertEqual(payload["subaccount"], "ACCT_test")

    def test_webhook_signature_is_verified(self):
        body = b'{"event":"charge.success"}'
        signature = hmac.new(
            server.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512
        ).hexdigest()
        self.assertTrue(server.valid_paystack_signature(body, signature))
        self.assertFalse(server.valid_paystack_signature(body, "0" * 128))

    def test_expired_payment_releases_listing_reservation(self):
        order_id, listing_id = self.create_pending_order()
        with server.connect_db() as db:
            db.execute("""
                UPDATE orders SET reservation_expires_at = '2000-01-01T00:00:00+00:00'
                WHERE id = ?
            """, (order_id,))
            db.execute("""
                UPDATE listings SET reservation_expires_at = '2000-01-01T00:00:00+00:00'
                WHERE id = ?
            """, (listing_id,))
            server.expire_pending_orders(db)
            order = db.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
            listing = db.execute("SELECT status, payment_pending_order_id FROM listings WHERE id = ?",
                                 (listing_id,)).fetchone()
        self.assertEqual(order["status"], "payment_expired")
        self.assertEqual(listing["status"], "active")
        self.assertIsNone(listing["payment_pending_order_id"])

    def test_successful_payment_marks_order_paid_and_listing_sold(self):
        _, listing_id = self.create_pending_order()
        handler = object.__new__(server.LoopHandler)
        result = handler.complete_paystack_order("loop_test_reference", "123")
        with server.connect_db() as db:
            order = db.execute(
                "SELECT status, paystack_transaction_id FROM orders WHERE payment_reference = ?",
                ("loop_test_reference",),
            ).fetchone()
            listing = db.execute("SELECT status FROM listings WHERE id = ?", (listing_id,)).fetchone()
        self.assertTrue(result["paid"])
        self.assertEqual(order["status"], "paid")
        self.assertEqual(order["paystack_transaction_id"], "123")
        self.assertEqual(listing["status"], "sold")

    def test_conflicting_success_requests_a_refund(self):
        self.create_pending_order(listing_status="sold")
        handler = object.__new__(server.LoopHandler)
        with patch.object(server, "paystack_request", return_value={"status": "processed"}) as request:
            result = handler.complete_paystack_order("loop_test_reference", "123")
        with server.connect_db() as db:
            order = db.execute(
                "SELECT status, refund_reference FROM orders WHERE payment_reference = ?",
                ("loop_test_reference",),
            ).fetchone()
        self.assertTrue(result["refunded"])
        self.assertEqual(order["status"], "refunded")
        self.assertTrue(order["refund_reference"].startswith("loop_refund_"))
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
