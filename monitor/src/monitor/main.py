import os

import uvicorn


def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("monitor.app:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
