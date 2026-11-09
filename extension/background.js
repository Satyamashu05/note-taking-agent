const NOTION_CLIENT_ID = "413d872b-594c-8188-a744-003724675851";

chrome.action.onClicked.addListener(async () => {
  const redirectUri = chrome.identity.getRedirectURL();
  const state = crypto.randomUUID();
  const authUrl = "https://api.notion.com/v1/oauth/authorize?" + new URLSearchParams({
    client_id: NOTION_CLIENT_ID, response_type: "code", owner: "user",
    redirect_uri: redirectUri, state,
  });
  const result = await chrome.identity.launchWebAuthFlow({ url: authUrl, interactive: true });
  const params = new URL(result).searchParams;
  if (params.get("state") !== state) throw new Error("state mismatch");
  console.log("redirect_uri:", redirectUri);
  console.log("code:", params.get("code"));
});
