"""Generate .env, users.yaml and redis/users.acl once per clone (stdlib only)."""

import argparse
import hashlib
import secrets
from pathlib import Path

DEMO_KEYS = {"conductor": "DEMO_PW_CONDUCTOR", "fc1": "DEMO_PW_FC1", "fc2": "DEMO_PW_FC2"}

ACL_TEMPLATE = """\
user default off resetkeys resetchannels -@all
user health on #{health} resetkeys resetchannels -@all +ping
user api on #{api} resetkeys resetchannels -@all +ping +client|setinfo +multi +exec +unwatch (~tc:NYX-* +xadd) (~cmdlock:* +set +get +del +watch) (~confirm:* +set +getdel) (~sess:* +set +get +del) (%R~ui:events %R~hb:* +xread +xrange +xrevrange +get +mget)
user sim on #{sim} resetkeys resetchannels -@all +ping +client|setinfo (%R~tc:NYX-* +xread +xrange) (~tc:NYX-* +xreadgroup +xack +xgroup|create) (~tm:NYX-* ~tcack:NYX-* +xadd)
user ingest on #{ingest} resetkeys resetchannels -@all +ping +client|setinfo +multi +exec +info (~tm:NYX-* ~tcack:NYX-* +xreadgroup +xack +xautoclaim +xclaim +xpending +xgroup|create +xinfo|stream +xinfo|groups +xinfo|consumers) (~tm:dlq ~ui:events +xadd) (~hb:* +set +get)
user admin on #{admin} ~* &* +@all
"""  # noqa: E501


def _example_keys(path: Path) -> list[str]:
    lines = (ln for ln in path.read_text().splitlines() if ln.strip() and ln[0] != "#")
    return [ln.split("=", 1)[0] for ln in lines]


def _read_env(path: Path) -> dict[str, str]:
    return dict(ln.split("=", 1) for ln in path.read_text().splitlines() if "=" in ln)


def _scrypt_entry(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**17, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=2**28, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def _user_names(path: Path) -> list[str]:
    lines = (ln.strip() for ln in path.read_text().splitlines())
    return [ln[2:].strip() for ln in lines if ln.startswith("- ")]


def _sha(env: dict[str, str], key: str) -> str:
    return hashlib.sha256(env[key].encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    root = parser.parse_args(argv).root

    env_path = root / ".env"
    if not env_path.exists():
        keys = _example_keys(root / ".env.example")
        env_path.write_text("".join(f"{k}={secrets.token_hex(24)}\n" for k in keys))
        env = _read_env(env_path)
        shown = " ".join(f"{name}={env[key]}" for name, key in DEMO_KEYS.items())
        print(f"demo users (shown once): {shown}")
    env = _read_env(env_path)

    users_path = root / "users.yaml"
    if not users_path.exists():
        names = _user_names(root / "users.example.yaml")
        entries = "".join(f'  {n}: "{_scrypt_entry(env[DEMO_KEYS[n]])}"\n' for n in names)
        users_path.write_text("users:\n" + entries)

    (root / "redis").mkdir(exist_ok=True)
    (root / "redis" / "users.acl").write_text(
        ACL_TEMPLATE.format(
            **{
                name: _sha(env, f"REDIS_{name.upper()}_PW")
                for name in ("health", "api", "sim", "ingest", "admin")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
