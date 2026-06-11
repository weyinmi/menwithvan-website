# Men With a Van Website

Production website and quote/booking backend for https://www.menwithvan.com.

## Deployment

Deployments are handled by GitHub Actions. A push to `main` builds a package from:

- `outputs/menwithvan-demo/`
- `outputs/menwithvan-backend/app.py`

The action uploads the package to the VPS and runs the server-side deploy helper:

```bash
sudo /usr/local/sbin/menwithvan-deploy-from-tar /tmp/menwithvan-deploy.tgz
```

The VPS keeps secrets such as Stripe, Google Maps and SMTP settings in `/etc/menwithvan/quote.env`; they are not stored in this repository.

## Booking Email

Customer and office booking confirmations are sent through Gmail SMTP using:

- `SMTP_USER=menwithvan4@gmail.com`
- `SMTP_FROM=Men With a Van <menwithvan4@gmail.com>`
- `OFFICE_EMAIL=menwithvan4@gmail.com`

Gmail requires a Google app password, not the normal Gmail password. The one-time helper below updates `/etc/menwithvan/quote.env` on the VPS and restarts the quote backend:

```bash
bash outputs/configure-menwithvan-gmail.sh
```
