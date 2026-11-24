"""CLI for elm-mcp — credential setup and testing."""

import base64
import getpass
import json
import os
import sys

import httpx

from .tls import _tls_verify

CRED_FILE = os.path.expanduser("~/.elm_creds.json")


def login() -> None:
    """Authenticate to ELM and save credentials."""
    url = input("ELM URL (e.g. https://elm.example.com): ").strip()
    if not url:
        print("❌ ELM URL is required.")
        sys.exit(1)
    username = input("Usuário ELM (LDAP): ").strip()
    password = getpass.getpass("Senha ELM: ")

    print(f"\nTestando autenticação em {url}...")
    try:
        client = httpx.Client(verify=_tls_verify(), timeout=30, follow_redirects=True)
        resp = client.post(
            f"{url}/jts/j_security_check",
            data={"j_username": username, "j_password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if "X-com-ibm-team-repository-web-auth-msg" in resp.headers:
            print("❌ Autenticação falhou. Verifique usuário/senha.")
            sys.exit(1)

        # Validate by fetching rootservices
        resp = client.get(f"{url}/ccm/rootservices", headers={"Accept": "application/rdf+xml"})
        if resp.status_code != 200:
            print(f"❌ Rootservices retornou {resp.status_code}")
            sys.exit(1)

        print("✅ Autenticação OK!")
    except httpx.ConnectError as e:
        print(f"❌ Erro de conexão: {e}")
        sys.exit(1)
    finally:
        client.close()

    # Save credentials
    creds = {
        "url": url,
        "username": username,
        "password": base64.b64encode(password.encode()).decode(),
    }

    with open(CRED_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(CRED_FILE, 0o600)

    print(f"✅ Credenciais salvas em {CRED_FILE} (senha em base64, chmod 600)")


def profile_draft(project: str) -> None:
    """Print a profile draft (JSON) for a CCM project, built from the discovery. Only reads (plus the login)."""
    import asyncio

    from . import oslc
    from .auth import close_client

    async def run() -> dict:
        try:
            return await oslc.build_profile_draft(project)
        finally:
            await close_client()

    try:
        draft = asyncio.run(run())
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(draft, indent=2, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login()
    elif len(sys.argv) > 1 and sys.argv[1] == "profile":
        if len(sys.argv) != 4 or sys.argv[2] != "draft":
            print("Uso: elm-mcp profile draft <project>")
            sys.exit(1)
        profile_draft(sys.argv[3])
    elif len(sys.argv) < 2 or sys.argv[1] == "serve":
        from .server import main as serve

        serve()
    else:
        print("Uso: elm-mcp [login|serve|profile draft <project>]")
        sys.exit(1)


if __name__ == "__main__":
    main()
