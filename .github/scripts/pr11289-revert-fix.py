import pathlib, sys

path = pathlib.Path("studio/src-tauri/src/desktop_auth.rs")
text = path.read_text()

before_client = """fn desktop_auth_client() -> Result<Client, reqwest::Error> {
    crate::loopback_http::client(Duration::from_secs(30))
}"""
after_client = """fn desktop_auth_client() -> Result<Client, reqwest::Error> {
    crate::loopback_http::client(Duration::from_secs(5))
}"""

before_retry = """async fn exchange_desktop_secret(
    client: &Client,
    port: u16,
    secret: &str,
) -> Result<Option<DesktopAuthResponse>, AuthError> {
    match exchange_desktop_secret_once(client, port, secret).await {
        Err(AuthError::Connectivity(_)) => {
            tokio::time::sleep(Duration::from_millis(500)).await;
            exchange_desktop_secret_once(client, port, secret).await
        }
        result => result,
    }
}"""
after_retry = """async fn exchange_desktop_secret(
    client: &Client,
    port: u16,
    secret: &str,
) -> Result<Option<DesktopAuthResponse>, AuthError> {
    exchange_desktop_secret_once(client, port, secret).await
}"""

for old, new in ((before_client, after_client), (before_retry, after_retry)):
    if old not in text:
        sys.exit("pattern not found; the PR implementation moved")
    text = text.replace(old, new, 1)

path.write_text(text)
print("reverted the PR implementation; tests left untouched")
