# Putting Murimi Farm ERP online (PythonAnywhere, free plan)

The free plan gives you `https://YOURNAME.pythonanywhere.com` with https, a site that never
sleeps, and 512 MB of storage that keeps your data. Two things to remember:

- **Once a month** open the **Web** tab and click **"Run until 1 month from today"**, or the site
  switches off. PythonAnywhere emails you a week before.
- The free plan can only reach some websites, so the automatic exchange rate may not work there.
  Type the rate in by hand under **Exchange rates** if the button says it can't reach the service.

Everywhere below, replace `YOURNAME` with the PythonAnywhere username you choose.

## 1. Put the code on GitHub (on your laptop, once)

1. On github.com, create a new **private** repository called `murimi-erp`. Don't add a README.
2. In a terminal:

```
cd C:\murimi-erp
git init
git add .
git commit -m "Murimi Farm ERP"
git branch -M main
git remote add origin https://github.com/promisetatendachataika-creator/murimi-erp.git
git push -u origin main
```

`.gitignore` keeps `db.sqlite3`, `.env`, `.venv` and `android-signing/` off GitHub. Check that
none of them appear on the GitHub page.

## 2. Create the PythonAnywhere account

Sign up for the free **Beginner** account at pythonanywhere.com. Your username becomes the address.

## 3. Copy the code onto PythonAnywhere

Open **Consoles → Bash** and run:

```
git clone https://github.com/promisetatendachataika-creator/murimi-erp.git
cd murimi-erp
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The repository is private, so git asks for your GitHub username and a password. For the password,
paste a GitHub **token** (GitHub → Settings → Developer settings → Fine-grained tokens → give it
read access to `murimi-erp` only).

## 4. The secret settings (.env)

Still in the Bash console:

```
python -c "import secrets; print(secrets.token_urlsafe(50))"
nano .env
```

Paste this, putting the long random line from above after `DJANGO_SECRET_KEY=`:

```
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=the-long-random-line
DJANGO_ALLOWED_HOSTS=YOURNAME.pythonanywhere.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://YOURNAME.pythonanywhere.com
DJANGO_SSL_REDIRECT=False
ANDROID_CERT_SHA256=(the SHA-256 line from android-signing/KEY-INFO.txt)
```

Save with Ctrl+O, Enter, then Ctrl+X. Then:

```
python manage.py migrate
python manage.py collectstatic --noinput
```

## 5. Switch the website on

1. **Web** tab → **Add a new web app** → **Manual configuration** → **Python 3.12**.
2. **Virtualenv:** enter `/home/YOURNAME/murimi-erp/.venv`
3. **WSGI configuration file:** click the link, delete everything in it, and paste:

```python
import os, sys
path = "/home/YOURNAME/murimi-erp"
if path not in sys.path:
    sys.path.insert(0, path)
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

4. **Static files:** add URL `/static/` with directory `/home/YOURNAME/murimi-erp/staticfiles`
5. **Security:** turn on **Force HTTPS**.
6. Click the green **Reload** button and open `https://YOURNAME.pythonanywhere.com`.

## 6. First use

1. Create your account on the website and set up the business.
2. To use the staff admin page, run this in the Bash console:
   `cd ~/murimi-erp && source .venv/bin/activate && python manage.py make_admin`

Your laptop copy and the online copy have separate databases. Start entering real farm records on
the online one.

## Updating after changes

```
cd ~/murimi-erp && git pull && source .venv/bin/activate
python manage.py migrate && python manage.py collectstatic --noinput
```

Then press **Reload** on the Web tab. Phones pick up the new version the next time they open the app.

## Backups

All your records are in one file, `/home/YOURNAME/murimi-erp/db.sqlite3`. Download it from the
**Files** tab every week and keep the copies safe.

## When there is money for hosting

Move to a paid plan (Render, Railway or PythonAnywhere paid) with PostgreSQL. The code already
supports that through the `POSTGRES_*` settings in `.env.example`.
