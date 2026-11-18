# Set up the daily signal review

This guide connects the daily review to STE's SharePoint and mail. When it is done, the R&A Business Partner and the Business Area Manager each get a private SharePoint list with an Approve and a Reject button on every row. They also get the Word, PowerPoint and Excel packs by email. [Spec 48](../specs/48_Daily_Two_Stage_Review.md) describes how the review works.

The pilot covers Urban Solutions. Hwaimay Mao is the R&A Business Partner. Gareth Tang is the Business Area Manager, but who would be replaced by Wilson Yeo (R&A Head) for the trial period.

## Before you start

You need these people:

- An STE Microsoft 365 or Entra administrator, for the app and its permissions.
- A SharePoint site owner, for the site and the lists.
- The GRIT-Scan operator, who edits `.env` and `config/` on the server.

The server must be able to reach `graph.microsoft.com` and `login.microsoftonline.com` on port 443.

## Create the site and the two lists

Each reviewer gets a separate list. Only that reviewer and the R&A owners can open it.

1. Create a SharePoint site named `GRIT-Scan Reviews`. Give the R&A owners group Full Control.
2. On the site, create a list named `URS R&A BP Review`.
3. Create a second list named `URS BA Manager Review`.
4. On each list, open **List settings** > **Permissions for this list** and select **Stop Inheriting Permissions**.
5. Remove every group except the R&A owners from each list.
6. Give Hwaimay Mao **Contribute** on the BP list only.
7. Give Wilson Yeo **Contribute** on the Manager list only.
8. Optional: apply the sensitivity label `Confidential` to the site, and turn off external sharing.

## Add the columns

Both lists use the same columns. To print the list, run this command on the server:

```bash
.venv/bin/python -m grit daily-review columns
```

1. On each list, create every column the command prints. Use the internal name exactly as printed, because GRIT writes to that name.
2. For `Decision`, create a Choice column with the choices `Pending`, `Approved` and `Rejected`. Set the default to `Pending`.
3. For `Locked`, create a Yes/No column with the default `No`.
4. For `Adverse`, create a Yes/No column. GRIT sets it to Yes when the signal appears in the latest READY adverse scan, or when people-risk lexicon terms hit a country where STE people are present (internal staff log). Optional: add a list view filtered to `Adverse` = Yes. A list created before this column existed still receives its rows: GRIT leaves the field out and records `missing_columns` in the publish step's outcome until you add the column.
5. Index `SignalId` and `ReviewDate` in **List settings** > **Indexed columns**.

## Add the Approve and Reject buttons

The buttons are column formatting. They set the decision, the reviewer and the time in one click, with no Power Apps or Power Automate.

1. On each list, open the `Decision` column header and select **Column settings** > **Format this column**.
2. Select **Advanced mode**.
3. Paste the contents of [decision-column.json](review/decision-column.json), then select **Save**.
4. Format the `SourceUrl` column the same way with [source-column.json](review/source-column.json). It shows a link to the page.
5. Create a view sorted by `ReviewDate` (descending), then `Position` (ascending). Make it the default view.

After GRIT closes a stage, `Locked` turns to `Yes` and the buttons disappear. GRIT ignores any change made after the deadline, even if someone edits the row.

## Register the app

GRIT uses one Entra app for both SharePoint and mail. If the RED duty mail already uses an app (`GRAPH_CLIENT_ID`), you can add these permissions to it.

1. In the Entra admin center, register an app named `GRIT-Scan`, or open the existing one.
2. Add the Microsoft Graph **application** permission `Sites.Selected`.
3. Add the Microsoft Graph **application** permission `Mail.Send`.
4. Select **Grant admin consent**.
5. Create a client secret. Give it to the operator through a secure channel, never by email or chat.

`Sites.Selected` alone gives no access. Grant the app write access to the review site only:

```http
POST https://graph.microsoft.com/v1.0/sites/SITE_ID/permissions
Content-Type: application/json

{
  "roles": ["write"],
  "grantedToIdentities": [{ "application": { "id": "CLIENT_ID", "displayName": "GRIT-Scan" } }]
}
```

Replace `SITE_ID` with the site ID, and `CLIENT_ID` with the app's client ID. An administrator with `Sites.FullControl.All` sends this request, for example from Graph Explorer.

> **Warning:** `Mail.Send` without a limit lets the app send as any mailbox in the tenant. Add an Exchange application access policy that limits the app to the sender mailbox, such as `grit-scan@stengg.com`.

## Find the site and list IDs

1. To get the site ID, send `GET https://graph.microsoft.com/v1.0/sites/TENANT.sharepoint.com:/sites/GRIT-Scan-Reviews`. Replace `TENANT` with STE's SharePoint host name. The `id` field is the site ID.
2. To get the list IDs, send `GET https://graph.microsoft.com/v1.0/sites/SITE_ID/lists`. Each list's `id` field is its list ID.

## Configure the server

1. In `.env`, set these variables. Never commit `.env`.

   ```text
   GRAPH_TENANT_ID=...
   GRAPH_CLIENT_ID=...
   GRAPH_CLIENT_SECRET=...
   GRAPH_MAIL_SENDER=grit-scan@stengg.com
   GRIT_SP_SITE_ID=...
   ```

2. In `config/review/urs.json`, set `listId` for `BP` and for `MANAGER`.
3. Optional: set `listUrl` for each stage. The emails then link straight to the list.
4. In `config/profiles/hwaimay.json` and `config/profiles/wilson.json`, set `email`.
5. Check the setup. This command prints the steps and makes no change:

   ```bash
   .venv/bin/python -m grit daily-review status --pack urs
   ```

## Schedule the review

The review runs from one cron line. The `tick` command runs every 15 minutes and does whatever step is due. It works out Singapore time itself, so the server's time zone does not matter.

```text
*/15 * * * * cd /path/to/grit-scan-platform && .venv/bin/grit daily-review tick >> output/daily-review.log 2>&1
```

Keep the existing `grit daily` line at 05:30. The review argues the signals that the daily lane catches.

## Run the first day by hand

Run a working day step by step before you add the cron line. Use a test site and test mailboxes for this run.

1. Run `.venv/bin/python -m grit daily-review step ARGUE --pack urs`. It argues today's signals with Gemini.
2. Run `.venv/bin/python -m grit daily-review step PUBLISH_BP --pack urs`. Check that the rows appear in the BP list.
3. Click Approve or Reject on a few rows.
4. Run `.venv/bin/python -m grit daily-review step CLOSE_BP --pack urs`. Check that the rows lock and the decisions show in `.venv/bin/python -m grit daily-review status`.
5. Run the remaining steps in order: `PUBLISH_MANAGER`, `MAIL_MANAGER`, `CLOSE_MANAGER` and `MAIL_FINAL`.

A step that is already done needs `--force` to run again.

## Before the lists exist

You can run the pilot before STE IT finishes. Without a list ID, GRIT still argues the signals and writes the packs to `output/review/`. A reviewer can record a decision from the command line:

```bash
.venv/bin/python -m grit daily-review decide --pack urs --stage BP --signal SIGNAL_ID --decision APPROVE --by "Hwaimay Mao"
```

Replace `SIGNAL_ID` with the ID in the Excel pack. The same deadlines apply.

## Keep the calendar current

`config/review/urs.json` lists Singapore public holidays that fall on a weekday. It holds the 2026 dates from the Ministry of Manpower. Add the 2027 dates when MOM publishes them. On a holiday, nothing is published or mailed. The holiday's signals join the next working day's review.
