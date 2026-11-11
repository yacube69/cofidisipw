# Users and sign-in (CIPW-59, CIPW-62)

**Built (0.3.1):** the common layer and **local accounts**, switched on with `auth.mode: local`. Without it (`none`, the default) Fides has no sign-in: anyone who can open it can upload PDFs and open every stored result, and a correction records no name. That is fine on one analyst's computer, not on a shared server.

**For Cofidis to choose** (question 4 in CIPW-61): stay with local accounts, or move to Active Directory or SSO (options 2–4 below). They reuse the same common layer, and the option is a setting, so a change later needs no other changes.

## The common layer (built)

- **The mode** is the setting `auth.mode`:
  - `none`: no sign-in, for one computer, development and the tests;
  - `local`: the accounts below;
  - `ldap`, `header` or `oidc` are reserved for the options Cofidis may choose.
- **Session:**
  - a random token in a cookie (`HttpOnly`, `SameSite=Strict`, `Secure` when the request came over HTTPS);
  - the database keeps only the token's SHA-256;
  - it ends after `auth.idle_minutes` without a request (120), after `auth.max_hours` in any case (12), at *Sign out*, and when the password changes or the account is disabled.
- **Protection:** every `/api/*` endpoint but `health` and `auth` answers 401 without a signed-in user, and 403 without the right role. The app then shows the sign-in page.
- **Wrong passwords:** after `auth.max_failures` (5) for one name, its sign-in is blocked for `auth.block_minutes` (15).
- **Roles:**

  | Role | May |
  | --- | --- |
  | *viewer* | open results and samples, download the PDF and JSON |
  | *analyst* | also upload PDFs and confirm or correct values; delete their own results |
  | *admin* | also delete any stored result (CIPW-60) |

- **The user is recorded** in the audit block of a result (`audit.user`, *Analysed by* in *About this result*), in the run log, and in every correction (`Correction.by`).
- **Who sees what** (`storage.visibility`): `team`, every signed-in user sees every stored result (the default); `own`, analysts and viewers see only their own and admins all. Samples are everyone's.

**Load on the server:** a request costs one read of its session in SQLite. The password hash (scrypt, about 16 MB and some tens of milliseconds) is computed only at sign-in. The last results stay in memory. The work of the server is the OCR, not the sign-in.

## The options

### 1. Local accounts (built)

Fides keeps its users in its database, `outputs/fides.db` (`storage.path`), next to the stored results. The folder is on the host with `docker compose` and is never in Git. A user has a name, a role, and the password as a scrypt hash only (Python's standard library).

**The admin account:** one account, `admin` (`auth.admin_name`), is created at the first start with `auth.mode: local`.
- **With a password in `.env`:** `FIDES_AUTH__ADMIN_PASSWORD=...`. It is applied at every start: changing it in `.env` and restarting resets a forgotten admin password, and enables the account again.
- **Without it:** Fides generates a password at the first start and writes it to `outputs/admin-password.txt`, next to the database. Sign in with it, change it, delete the file.
- **No password is in the code or the repository:** a password committed in Git would open every Fides server to anyone who has the code.

**More users**, in the folder of the code or inside the container (`docker compose exec fides ...`):

```bash
python scripts/users.py add jnovak --role analyst     # asks for the password twice (at least 10 characters)
python scripts/users.py password jnovak               # a new password; signs the user out everywhere
python scripts/users.py role jnovak admin
python scripts/users.py disable jnovak                # leavers; enable undoes it
python scripts/users.py list
```

| | |
| --- | --- |
| Needs from Cofidis | nothing |
| For the user | one more password |
| Leavers | an admin disables them |
| New dependency | none |

Good for a pilot, a demo, or a server outside the domain.

### 2. Active Directory: name and password (LDAP)

The sign-in page asks for the user's AD name and password. Fides checks them with a domain controller over LDAPS (`ldaps://dc.cofidis.local`) and reads the user's AD groups. The groups map to the roles, e.g. `FIDES-Analysts` to *analyst*. The password is never stored.

| | |
| --- | --- |
| Needs from Cofidis | the LDAPS address and its CA certificate, the base DN, the AD groups for the roles; possibly a read-only service account to look users up |
| For the user | the same name and password as for Windows |
| Leavers | handled by AD |
| New dependency | `ldap3` (pure Python) |
| Effort | **0.5–1 day** on the common layer |

### 3. Active Directory: Windows sign-on (Kerberos, through a reverse proxy)

The analyst opens Fides from a PC in the domain and is signed in without typing anything.
- The sign-on is done by a reverse proxy in front of Fides: IIS, or Apache/nginx with Kerberos (SPNEGO).
- The proxy passes the user and their groups to Fides in a header.
- Fides accepts that header only from the proxy's address, and the port of Fides is closed to everything else.

| | |
| --- | --- |
| Needs from Cofidis | the proxy with Kerberos (a service principal and keytab for the Fides address), the group header, the AD groups for the roles |
| For the user | nothing to type |
| Leavers | handled by AD |
| New dependency | none in Fides (the work is in the proxy configuration, by Cofidis' IT) |
| Effort | **0.5 day** in Fides, on the common layer |

### 4. SSO with OpenID Connect (Microsoft Entra ID or AD FS)

*Sign in* goes to Microsoft's sign-in page, with MFA if Cofidis uses it, and back to Fides.
- **Flow:** authorization code with PKCE.
- **The ID token** comes straight from the token endpoint over HTTPS. Fides checks its issuer, audience, expiry and nonce.
- **Roles** come from the group or app-role claims.
- **Identity providers:** the same code works with Entra ID (cloud), AD FS (on the server side) and Keycloak.

| | |
| --- | --- |
| Needs from Cofidis | an app registration: client ID and secret, the redirect URI `https://<fides address>/api/auth/callback`, group or app-role claims for the roles; the server must reach the sign-in service |
| For the user | the company sign-in, MFA included |
| Leavers | handled centrally |
| New dependency | none (the HTTP client Fides already has) |
| Effort | **1 day** on the common layer |

## Comparison

| | Local | AD, LDAP | AD, Windows sign-on | SSO (OIDC) |
| --- | --- | --- | --- | --- |
| Typing a password in Fides | yes, its own | yes, the AD one | no | no, Microsoft's page |
| Work for Cofidis' IT | none | small | medium (proxy, Kerberos) | small (app registration) |
| MFA | no | no | domain PC | yes |
| Works outside the domain | yes | yes | no | yes |
| Offboarding | by hand | automatic | automatic | automatic |

**Our recommendation:** local accounts (built) for the pilot and the demo, then the option Cofidis chooses. If Cofidis has Entra ID or AD FS, SSO is the most convenient for its users, and its IT knows it from other internal applications.

## For every option

- **HTTPS** in front of Fides, with a certificate on the proxy. A password or session must never travel over plain HTTP. On one's own computer (`localhost`) this is not needed.
- **Stored results** (CIPW-60) decide whether an analyst sees only their own results or the team's (question 5 in CIPW-61).
