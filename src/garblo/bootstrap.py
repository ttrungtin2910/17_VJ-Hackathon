from garblo.config import get_settings
from garblo.storage import ObjectStorage


def main() -> None:
    ObjectStorage(get_settings()).ensure_bucket()


if __name__ == "__main__":
    main()
