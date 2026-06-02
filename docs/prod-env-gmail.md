# Production Gmail SMTP setup for Kindle

Use this when the bot is private/family-only and Amazon SES is unnecessary or not approved yet.

## 1. Gmail account

Use a dedicated sender mailbox, not a personal mailbox.

Required:
- Google account with 2-Step Verification enabled.
- Google app password created for this bot.
- `SMTP_FROM_EMAIL` should usually be the same mailbox as `SMTP_USERNAME`.

Never commit `.env`, the app password, Telegram token, OpenAI key, or Tavily key.

## 2. Bot `.env`

Start from `.env.gmail.example` or `.env.production.example`.

Important SMTP values:

```env
SMTP_PROVIDER=gmail
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your.dedicated.gmail@gmail.com
SMTP_PASSWORD=your-google-app-password
SMTP_FROM_EMAIL=your.dedicated.gmail@gmail.com
SMTP_STARTTLS=true
KINDLE_WORKER_CONCURRENCY=1
KINDLE_USER_CONCURRENCY=1
```

The bot treats Gmail as generic SMTP. It does not use the Gmail API.

## 3. User Kindle setup

Each user must:
1. Open Amazon Kindle Personal Document settings.
2. Find their Kindle e-mail address.
3. Add `SMTP_FROM_EMAIL` to **Approved Personal Document E-mail List**.
4. Open `⚙️ Kindle` in the bot, save Kindle e-mail, confirm sender approval, then optionally send a test.

## 4. Troubleshooting

- `SMTP не авторизовался`: check that 2-Step Verification is enabled and the app password is pasted without extra spaces.
- Kindle mail is not visible: check Amazon approved sender list and spam/delivery delay.
- Large files fail: keep `KINDLE_MAX_ATTACHMENT_MB=28`; e-mail encoding inflates attachments.
- Gmail quotas are lower than transactional e-mail providers. Keep concurrency at `1` for private use.

## 5. Future providers

`SMTP_PROVIDER=custom`, `gmail`, `google_workspace`, `zoho`, `brevo`, `mailgun`, `amazon_ses`, and `disabled` are supported as configuration modes. Amazon SES remains optional, not the default.
