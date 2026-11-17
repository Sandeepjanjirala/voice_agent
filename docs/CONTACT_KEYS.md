# Contact keys: backup, recovery and rotation

Contact phone numbers are encrypted with keys that live outside the database.
Losing the keys loses every stored number. Leaking them with a database copy
exposes every number. This page says how to keep them safe.

## The two keys

| Setting | What it does | If lost | If leaked |
| :--- | :--- | :--- | :--- |
| `CONTACT_ENCRYPTION_KEYS` | Encrypts and decrypts numbers (Fernet). Comma-separated, newest first | Numbers cannot be read again. Contacts stay, without numbers | Anyone with a database copy can read the numbers: rotate at once |
| `CONTACT_LOOKUP_KEY` | Keyed hash for duplicates and the do-not-call list | Rebuild with `contactkeys rehash` (needs the encryption keys) | Numbers can be guessed by trying candidates: change it and rehash |

Both are read from `.env.local` in development. In production they belong in a
secrets manager (for example Azure Key Vault, AWS Secrets Manager or Google
Secret Manager), passed to Django as environment variables.

## Rules

1. Never commit the keys. `.env.local` is git-ignored, and no key value has
   ever been committed to this repository (checked 17 Nov 2026).
2. Never put the keys in the same backup as the database. A database backup
   without the keys contains no readable phone number.
3. Keep at least two copies of the keys, in separate places, for example a
   password manager and a sealed offline copy held by the organisation owner.

## Back up the keys

```powershell
# The two lines to copy into the password manager or vault:
Select-String -Path .env.local -Pattern '^CONTACT_(ENCRYPTION_KEYS|LOOKUP_KEY)='

# Fingerprints identify the keys without revealing them:
.\.venv\Scripts\python.exe manage.py contactkeys fingerprint
```

Write the fingerprints next to the backup copy. To check a backup later, load
it into a scratch `.env.local` on another machine and run `fingerprint` again:
equal fingerprints mean the same keys.

## Recover after losing `.env.local`

1. Put both lines back into `.env.local` from the backup.
2. Run `.\.venv\Scripts\python.exe manage.py contactkeys fingerprint` and compare with the fingerprints kept with the backup.
3. Run `.\.venv\Scripts\python.exe manage.py contactkeys status`. It must say
   "Every stored number can be read." If it reports unreadable numbers, an
   older encryption key is missing from the list: find it and add it after the
   newest one.

## Restore a database backup

Restore the database, then put back the keys that were in use when the backup
was made. If the keys were rotated since, list the old key after the new one
in `CONTACT_ENCRYPTION_KEYS`, run `contactkeys status`, then `contactkeys rotate`.

## Rotate the encryption key (yearly, or at once after a leak)

```powershell
.\.venv\Scripts\python.exe manage.py contactkeys generate        # copy only the CONTACT_ENCRYPTION_KEYS value
# In .env.local: CONTACT_ENCRYPTION_KEYS=<new key>,<old key>      newest first
# Restart Django, then:
.\.venv\Scripts\python.exe manage.py contactkeys rotate --dry-run
.\.venv\Scripts\python.exe manage.py contactkeys rotate
.\.venv\Scripts\python.exe manage.py contactkeys status          # "older key: 0"
```

Keep the old key in the list until database backups made before the rotation
have expired; then remove it and back up the new list.

## Change the lookup key (only after a leak)

```powershell
# In .env.local: CONTACT_LOOKUP_KEY=<new value from contactkeys generate>
.\.venv\Scripts\python.exe manage.py contactkeys rehash --dry-run
.\.venv\Scripts\python.exe manage.py contactkeys rehash
```

Duplicate detection and the do-not-call list do not match numbers between
changing the key and running `rehash`, so do both within the same few minutes,
with no imports or calls in between.
