# Users and sign-in: the options for Cofidis (CIPW-59)

Today Fides has **no sign-in**. Anyone who can open it can upload PDFs and open the last 20 results, and a correction records no name (`Correction.by` is empty). That is fine on one analyst's computer. On a shared Cofidis server it is not.

Cofidis chooses how its users sign in (question 4 in CIPW-61). All options share one common layer, and the option is a setting, so it can change later without other changes.

## What every option gets: the common layer

- **The mode** is the setting `auth.mode`:
  - `none`: today's behaviour, for one computer, development and the tests;
  - `local`, `ldap`, `header` or `oidc`: the options below.
- **Session:** a cookie (`HttpOnly`, `SameSite=Strict`, `Secure` on HTTPS) that expires after a set idle time, and *Sign out*.
- **Protection:** every `/api/*` endpoint answers 401 without a signed-in user, and 403 without the right role.
- **Roles:**

  | Role | May |
  | --- | --- |
  | *viewer* | open results and samples, download the PDF and JSON |
  | *analyst* | also upload PDFs and confirm or correct values |
  | *admin* | also manage local users and delete stored results (CIPW-60) |

- **The user is recorded** in every correction (`Correction.by`), in the audit block of a result and in the run log.

Effort for the common layer: **about 1 day**, with tests and docs.

## The options

### 1. Local accounts

Fides keeps its own users in a file on the server, `config/users.yaml`, outside the image and never in Git. The file holds the name, the role and the password as a scrypt hash (Python's standard library). An admin adds users with `python scripts/users.py add jnovak --role analyst`, which asks for the password, and removes them the same way.

| | |
| --- | --- |
| Needs from Cofidis | nothing |
| For the user | one more password |
| Leavers | an admin removes them by hand |
| New dependency | none |
| Effort | common layer + **0.5 day** |

Good for a pilot, a demo, or a server outside the domain.

### 2. Active Directory: name and password (LDAP)

The sign-in page asks for the user's AD name and password. Fides checks them with a domain controller over LDAPS (`ldaps://dc.cofidis.local`) and reads the user's AD groups. The groups map to the roles, e.g. `FIDES-Analysts` to *analyst*. The password is never stored.

| | |
| --- | --- |
| Needs from Cofidis | the LDAPS address and its CA certificate, the base DN, the AD groups for the roles; possibly a read-only service account to look users up |
| For the user | the same name and password as for Windows |
| Leavers | handled by AD |
| New dependency | `ldap3` (pure Python) |
| Effort | common layer + **0.5–1 day** |

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
| Effort | common layer + **0.5 day** in Fides |

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
| Effort | common layer + **1 day** |

## Comparison

| | Local | AD, LDAP | AD, Windows sign-on | SSO (OIDC) |
| --- | --- | --- | --- | --- |
| Typing a password in Fides | yes, its own | yes, the AD one | no | no, Microsoft's page |
| Work for Cofidis' IT | none | small | medium (proxy, Kerberos) | small (app registration) |
| MFA | no | no | domain PC | yes |
| Works outside the domain | yes | yes | no | yes |
| Offboarding | by hand | automatic | automatic | automatic |

**Our recommendation:** build the common layer and the local accounts first. They are needed anyway for a pilot and the demo. Then add the option Cofidis chooses. If Cofidis has Entra ID or AD FS, SSO is the most convenient for its users, and its IT knows it from other internal applications.

## For every option

- **HTTPS** in front of Fides, with a certificate on the proxy. A password or session must never travel over plain HTTP. On one's own computer (`localhost`) this is not needed.
- **Stored results** (CIPW-60) decide whether an analyst sees only their own results or the team's (question 5 in CIPW-61).
