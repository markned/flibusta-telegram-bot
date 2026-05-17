from aiosmtplib.errors import SMTPAuthenticationError, SMTPRecipientsRefused, SMTPResponseException
from app.services.email_sender import EmailConfigurationError
import httpx

def classify_smtp_error(exc):
 if isinstance(exc,SMTPAuthenticationError): return 'auth_error'
 if isinstance(exc,EmailConfigurationError): return 'config_error'
 if isinstance(exc,(httpx.TimeoutException,httpx.NetworkError)): return 'temporary_failure'
 if isinstance(exc,SMTPRecipientsRefused): return 'recipient_rejected'
 if isinstance(exc,SMTPResponseException):
  if exc.code in {552,554}: return 'message_too_large'
  if exc.code in {421,450,451,452}: return 'temporary_failure'
  if exc.code==454: return 'throttled'
  if 500<=exc.code<600: return 'sender_rejected'
 return 'unknown_failure'
def smtp_user_message(cat):
 return {'auth_error':'Kindle sending is temporarily unavailable.','config_error':'Kindle sending is temporarily unavailable.','recipient_rejected':'Delivery failed. Check your Kindle address and make sure the bot sender address is approved in Amazon.','sender_rejected':'Delivery failed. Check your Kindle address and make sure the bot sender address is approved in Amazon.','message_too_large':'This file is too large to send to Kindle by e-mail.','throttled':'Amazon SES is temporarily throttling delivery. I will retry automatically.','temporary_failure':'Amazon SES is temporarily unavailable. I will retry automatically.'}.get(cat,'Failed to send this book to Kindle. Try again later.')
def is_transient(cat): return cat in {'throttled','temporary_failure'}
