from __future__ import annotations

import uvicorn
from alembic import command
from alembic.config import Config

from fastsocial.config import settings


def main() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    uvicorn.run("fastsocial.app:app", host="0.0.0.0", port=settings().port, reload=False)


if __name__ == "__main__":
    main()
