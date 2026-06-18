# Men With a Van Website

Production website and quote/booking backend for https://www.menwithvan.com.

## Deployment

Production updates are handled directly with `./synchronize`. It builds a package from:

- `outputs/menwithvan-demo/`
- `outputs/menwithvan-backend/app.py`

The helper uploads the package to the VPS using a pinned SSH host key and a unique temporary package path, then runs the server-side deploy helper.

```bash
./synchronize
```

The VPS keeps secrets such as Stripe, Google Maps and SMTP settings in `/etc/menwithvan/quote.env`; they are not stored in this repository.

## Direct VPS Sync

Use `synchronize` when you want to work locally and synchronise directly with the VPS:

```bash
./synchronize setup-ssh
./synchronize
./synchronize status
./synchronize pull
./synchronize push
```

- `./synchronize setup-ssh` installs password-free VPS access for this project. Run it once and enter the VPS root password if asked.
- `./synchronize` uploads the local frontend and backend app to the VPS. It is the same as `./synchronize push`.
- `./synchronize status` checks local unpublished changes and whether the VPS is reachable.
- `./synchronize pull` downloads the live frontend and backend app from the VPS into `outputs/`, after creating a local backup in `work/synchronize-backups/`.
- `./synchronize push` uploads the local frontend and backend app through the existing VPS deploy helper, which creates a server-side backup before replacing live files.

`synchronize` does not pull secrets, Stripe keys, Google keys, email passwords or the booking database.

## Booking Email

Customer and office booking confirmations are sent through Gmail SMTP using:

- `SMTP_USER=menwithvan4@gmail.com`
- `SMTP_FROM=Men With a Van <menwithvan4@gmail.com>`
- `OFFICE_EMAIL=menwithvan4@gmail.com`

Gmail requires a Google app password, not the normal Gmail password. The one-time helper below updates `/etc/menwithvan/quote.env` on the VPS and restarts the quote backend:

```bash
bash outputs/configure-menwithvan-gmail.sh
```

## Stripe Account Switch

Stripe settings live only on the VPS in `/etc/menwithvan/quote.env`. To switch to a different Stripe account, use matching keys from the same account and mode:

- `STRIPE_PUBLISHABLE_KEY` (`pk_test_...` or `pk_live_...`)
- `STRIPE_SECRET_KEY` (`sk_test_...` or `sk_live_...`)
- `STRIPE_WEBHOOK_SECRET` (`whsec_...`)

Create the webhook endpoint in Stripe for:

```text
https://www.menwithvan.com/api/stripe/webhook
```

Then run:

```bash
bash outputs/configure-menwithvan-stripe.sh
```
