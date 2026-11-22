# Loop campus exchange

Loop is a campus-first marketplace prototype with sale, swap, and item-request
listings. It can use the existing local SQLite database or Supabase Auth,
Postgres, and Storage.

## Run locally with the existing SQLite database

1. Install the Python dependency: `python -m pip install -r requirements.txt`.
2. Leave `LOOP_BACKEND=sqlite` in `.env` to keep using your existing local
   database, then run `python server.py` (or `py server.py` on Windows).
3. Open `http://127.0.0.1:8000`.
4. Create an account with an `@pau.edu.ng` email address and a password of at
   least 12 characters.

SQLite remains the default unless `LOOP_BACKEND=supabase` is set. Its database
is `data/loop.sqlite3`, and uploaded images are in `data/uploads/`. The current
SQLite data is not altered by enabling Supabase.

## Use Supabase

1. Create a Supabase project.
2. Open the Supabase SQL Editor and run [`supabase_schema.sql`](./supabase_schema.sql).
   It creates the profile trigger, application tables, indexes, restrictive
   row-level security defaults, and the public `loop-listing-images` bucket.
3. In the Supabase dashboard, copy the project URL, anon/public key, and a
   Postgres connection string. For Render or another IPv4-only host, use the
Supabase session pooler connection string. URL-encode special characters
in its password. Keep the database password and service-role key private.
4. To switch this local server to Supabase, fill in the private `.env` file in
   the project folder and change `LOOP_BACKEND` to `supabase`. It is ignored by
   Git and Docker; `.env.example` is the shareable template. Set:

   - `LOOP_BACKEND=supabase`
   - `SUPABASE_URL`
   - `SUPABASE_PUBLISHABLE_KEY`
   - `SUPABASE_SECRET_KEY`
   - `SUPABASE_DB_URL`
   - `LOOP_ADMIN_EMAIL` (optional moderator account email)

   Leave `SESSION_COOKIE_SECURE=false` for local HTTP. The server loads `.env`
   automatically; it will report missing Supabase settings until you fill them.

   Never put the secret key or database connection string in browser code,
   source control, or a public chat. On Render, set the values as private
   environment variables in the service settings.
5. In Supabase Auth settings, set the application Site URL and allowed redirect
   URLs to the app's real address. Enable email confirmation and configure
   email delivery if you want users to verify their mailbox. Loop always
   restricts registration and login to `@pau.edu.ng`; Supabase Auth handles
   password storage, sessions, and optional verification.

The app's server uses the Postgres connection string and secret key only
on the server. Browser requests continue to go through the Loop API. The
secret key is privileged and must remain secret. Listing images are
stored in the public Supabase Storage bucket, so anyone with an image URL can
view those images.

**No SQLite records are imported.** The Supabase tables start empty; the
existing SQLite database and its sample listings remain on disk. Supabase
startup checks that the schema has been applied and fails with an error if
configuration or tables are missing.

## Features

- PAU-domain accounts, profile-based identity, optional Supabase email
  confirmation, expiring authentication tokens, and CSRF checks.
- Search, category and listing-type filters, price/date sorting, personal
  listings, and listing create/edit/delete.
- JPEG, PNG, and WebP image uploads (3 MB maximum).
- Sale, swap, and item-request listings. Sale listings can use the demo
  checkout; swap and request listings invite users to message and arrange an
  exchange.
- Private buyer/seller conversations with unread counts.
- Listing reports with an admin review queue; set `LOOP_ADMIN_EMAIL` to the
  account email that should review reports.
- Recorded demo checkout: listings are marked sold and orders are recorded,
  but no payment details are collected and no money moves.
- Responsive interface and SQLite or Supabase persistence.

## Deploy to Render

1. Rotate any database password that has been shared in chat, then update
   `SUPABASE_DB_URL` with the new password. Do not commit `.env` or send
   service-role/database credentials in chat.
2. Create a private GitHub repository and push the project files. Confirm
   `.env` is not included; only `.env.example` should be shared.
3. Before deploying, run `supabase_schema.sql` in the Supabase SQL Editor.
   The database starts empty; the existing SQLite records are not imported.
4. In Render, choose **New → Blueprint**, connect the GitHub repository, and
   select its `render.yaml`. The blueprint configures the Docker web service
   to use Supabase. Add the requested environment values in Render's
   environment settings:
   `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, and
   `SUPABASE_DB_URL`. Also set `LOOP_ADMIN_EMAIL` if you want a moderator.
   Use the Supabase session-pooler connection string for Render; store the
   database password only in Render's private environment settings.
5. Start the deployment after the schema and environment variables are ready.
   The service health check is `/api/health`. When Render gives the service a
   public URL, set that URL as the Supabase Auth Site URL and add it to the
   allowed redirect URLs. Configure Supabase email delivery if account
   verification emails should be sent.

No Render disk is required for application data or images. This workspace is
not currently connected to a GitHub repository, so the deployment must be
started from your Git provider after pushing the project.

## Important limitations

This is a prototype, not a production-ready marketplace or independently
security-audited service. Demo checkout never charges a card or transfers
money. Do not collect real payment details or rely on this prototype for
financial transactions. Before a real launch, commission a security review,
configure operational monitoring/backups and abuse controls, and integrate a
payment provider through its official server-side SDK and privately managed
credentials.
