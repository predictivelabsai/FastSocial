# FastSocial OAuth Plan

This plan covers the first three direct social integrations: X, LinkedIn, and Facebook Pages.
FastSocial's production service URL is `https://fastsocial.org`.

## Security and browser collaboration

Do not send social-network passwords, MFA codes, recovery codes, or session cookies in chat or add
them to `.env`. Codex can drive the developer portals in a visible browser session, while the account
owner enters credentials, completes MFA, grants consent, and confirms any security-sensitive action.

Application client IDs and secrets belong in the project-root `.env`, which is excluded from Git.
OAuth access and refresh tokens obtained by FastSocial are encrypted before database storage.

## Recommended sequence

1. Configure and verify X.
2. Configure and verify LinkedIn member publishing.
3. Implement direct Meta OAuth and Facebook Page selection.
4. Configure and verify a Facebook Page.
5. Prepare provider review materials before enabling connections for users outside the developer
   accounts and test roles.

## X

FastSocial already implements OAuth 2.0 Authorization Code flow with PKCE, state validation, profile
lookup, encrypted token storage, and refresh-token storage.

### Developer portal configuration

- Use a Web App/confidential OAuth 2.0 client.
- Enable OAuth 2.0 Authorization Code with PKCE.
- Configure read and write access.
- Register this exact callback URL:

  ```text
  https://fastsocial.org/oauth/x/callback
  ```

- Set the website URL to `https://fastsocial.org`.
- Allow the scopes `tweet.read`, `tweet.write`, `users.read`, and `offline.access`.

### Required FastSocial configuration

```env
X_CLIENT_ID=...
X_CLIENT_SECRET=...
```

`offline.access` is required so X issues a refresh token. X requires an exact callback URL match.

### Verification

1. Sign in to FastSocial and open Integrations.
2. Select X direct OAuth.
3. Complete X consent in the visible browser.
4. Confirm that the X account appears as connected.
5. Publish a controlled text-only test post.
6. Verify the returned post ID and open the post on X.
7. Verify token refresh separately before relying on scheduled publishing.

Official reference: <https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code>

## LinkedIn

FastSocial already implements three-legged OAuth, state validation, OpenID profile lookup, encrypted
token storage, and publishing through LinkedIn's Posts API.

### Developer portal configuration

- Add the **Sign in with LinkedIn using OpenID Connect** product.
- Add the **Share on LinkedIn** product.
- Register this exact redirect URL:

  ```text
  https://fastsocial.org/oauth/linkedin/callback
  ```

- Confirm access to `openid`, `profile`, `email`, and `w_member_social`.

### Required FastSocial configuration

```env
LINKEDIN_CLIENT_ID=...
LINKEDIN_CLIENT_SECRET=...
```

### Initial scope

The current implementation publishes as the authenticated LinkedIn member. Publishing as a company
Page is a separate phase because it requires organization permissions, administrator validation,
organization discovery, and a Page-selection UI.

### Verification

1. Select LinkedIn direct OAuth in FastSocial Integrations.
2. Complete LinkedIn consent in the visible browser.
3. Confirm that the member account appears as connected.
4. Publish a controlled text-only member post.
5. Verify the returned LinkedIn post ID.
6. Test one supported image post.
7. Confirm the token-expiry behavior before enabling scheduled publishing.

Official references:

- <https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access>
- <https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow>

## Facebook Pages

FastSocial does not yet implement direct Meta OAuth. The current Facebook integration supports
managed Arcade or Composio connections, so direct Facebook must be built before portal setup can be
verified end to end.

### Proposed callback and configuration

Register this callback after the route is implemented:

```text
https://fastsocial.org/oauth/facebook/callback
```

Add these settings to FastSocial:

```env
META_APP_ID=...
META_APP_SECRET=...
META_GRAPH_API_VERSION=...
```

The Graph API version must be explicit and reviewed during upgrades rather than silently following
Meta's latest version.

### Initial permissions

Request only the permissions needed for the first Facebook Page workflow:

```text
public_profile
pages_show_list
pages_read_engagement
pages_manage_posts
```

Additional permissions for comments, inbox, ads, Insights, Instagram, or organization-wide use
should be introduced only with the matching product feature and review evidence.

### Implementation work

1. Add Meta configuration fields and production validation.
2. Add OAuth start and callback routes with cryptographically random state validation.
3. Exchange the authorization code for a user access token.
4. Where supported, exchange it for the appropriate longer-lived token.
5. Query `/me/accounts` to retrieve Pages the user can manage and their permitted tasks.
6. Present an explicit Page-selection screen when more than one Page is available.
7. Store the selected Page ID, display name, granted scopes/tasks, and encrypted Page token.
8. Add token health checking, expiry/error reporting, disconnect, and reauthorization behavior.
9. Add direct Facebook Page publishing in the provider client.
10. Add unit tests with mocked Graph API responses and an authenticated browser smoke test.

### Meta portal and review preparation

- Create or select the appropriate Meta business app.
- Add Facebook Login for the web and configure the exact redirect URI.
- Add the account owner as an administrator, developer, or tester during development.
- Associate the Facebook Page and business assets needed for testing.
- Configure the app domain, privacy-policy URL, terms URL where applicable, and user-data deletion
  instructions or callback.
- Complete business verification if Meta requires it for the requested access.
- Request Advanced Access/App Review before allowing people outside app roles to connect.
- Prepare a reviewer account, written steps, and a screen recording showing why each permission is
  needed and how the resulting data is used.

### Verification

1. Complete Meta consent using an app-role account.
2. Confirm that only Pages the user can manage are offered.
3. Select the intended Page and verify its granted tasks.
4. Publish a controlled unpublished/draft test where supported, otherwise a clearly identified live
   test post that can be removed immediately.
5. Verify readback, post ID storage, token health, disconnect, and reauthorization.
6. Repeat with a non-role test user only after the required permissions receive Advanced Access.

Official Meta Pages API collection:
<https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api>

## Information needed from the account owner

For X:

- Which X developer Project/App should FastSocial use.
- Confirmation that the app may publish to the intended X account.
- Client ID and, for a confidential Web App, Client Secret added directly to `.env`.

For LinkedIn:

- Which LinkedIn developer application should FastSocial use.
- The LinkedIn company Page associated with that application.
- Whether phase one needs personal-member publishing only or must include company Page publishing.
- Client ID and Client Secret added directly to `.env`.

For Facebook:

- Which Meta developer app and Business Portfolio should own the integration.
- Which Facebook Page will be used for controlled testing.
- Whether phase one is Page publishing only; this is recommended before adding comments, inbox,
  Insights, Ads, or Instagram.
- Meta App ID and App Secret added directly to `.env` after the implementation fields exist.

The account owner should enter all login credentials and MFA responses personally in the visible
browser. FastSocial does not need or accept the underlying X, LinkedIn, or Facebook passwords.
