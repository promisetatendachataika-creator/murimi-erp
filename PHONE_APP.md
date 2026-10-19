# The Murimi phone app

**Android** gets an APK file, shared by WhatsApp, Quick Share or a download link. It is not on the
Play Store. **iPhones** use "Add to Home Screen" in Safari.

## How it works

- The app is the Murimi website in a full-screen window (an Android "Trusted Web Activity").
  When the website is updated, the app is updated too. Nobody needs to reinstall.
- **Offline:** pages you have opened before are kept on the phone. With no signal you can still use:
  - the work register
  - stock used, received, moved and counted
  - fuel fills
  - harvests

  These records wait on the phone in an "outbox" (see **Phone & sync** in the menu) and are sent
  when there is network.
- Every record carries its own id, so a record sent twice on a weak network is saved once.
- If Murimi refuses a record, it stays on the phone marked "needs you", with the reason. For
  example, a supervisor recorded using 10 bags while offline but only 6 were left. You can try it
  again, or throw it away.
- Invoices, payroll and settings need the network.

## Building the APK (GitHub does it)

You do this once, after the website is online (see `DEPLOY_PYTHONANYWHERE.md`).

1. In the `murimi-erp` repository on GitHub, open **Settings → Secrets and variables → Actions**.
2. Under **Variables**, add `APP_HOST` with the value `YOURNAME.pythonanywhere.com` (no https://).
3. Under **Secrets**, add three secrets, using the files in `C:\murimi-erp\android-signing\`:
   - `KEYSTORE_BASE64`: everything inside `murimi-erp-keystore.base64.txt`
   - `KEYSTORE_PASSWORD`: from `KEY-INFO.txt`
   - `KEY_PASSWORD`: from `KEY-INFO.txt`
4. Open **Actions → Build Android app → Run workflow**. After about 5 minutes, open the finished
   run and download **murimi-erp-apk-N**, a zip with `murimi-erp.apk` inside.

## Sharing and installing

- **WhatsApp / Quick Share:** send `murimi-erp.apk`. On the phone, tap it and allow "install unknown
  apps" for WhatsApp or Files when Android asks.
- If Play Protect warns about an unknown developer, tap **More details → Install anyway**. This is
  normal for apps not on the Play Store.
- **Download from the website:** upload `murimi-erp.apk` on PythonAnywhere's **Files** tab into
  `/home/YOURNAME/murimi-erp/staticfiles/`. The **Phone & sync** page then shows a "Download the
  app" button.
- **iPhone:** open the website in Safari → Share → **Add to Home Screen**.

## The signing key: important

`android-signing/murimi-erp.jks` proves new versions come from you.

- Keep it secret: it is in `.gitignore`, so it never goes to GitHub.
- Back it up on a USB stick or in private cloud storage.
- If it is lost, phones can't update the app, and everyone would have to uninstall and reinstall.

Its SHA-256 fingerprint is in the server's `.env` (`ANDROID_CERT_SHA256`), which lets Android open
the site full-screen with no address bar.

## When a new APK is needed

Only when something in `android/` changes, such as the icon, the name or the website address.
Pushing those changes makes GitHub build a new APK automatically. Normal website changes do not
need a new APK.
