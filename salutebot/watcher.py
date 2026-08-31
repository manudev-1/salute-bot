import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from salutebot.alerts import TelegramSender
from salutebot.config import EnvConfig
from salutebot.crypto import Crypto
from salutebot.daemon import run
from salutebot.scraper.drive import LiveScraper
from salutebot.store import Store


LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "watcher.log"


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)


logger = logging.getLogger("salutebot.watcher")


def main():
    setup_logging()

    logger.info("========================================")
    logger.info("salute-bot watcher starting")
    logger.info("========================================")

    db_path = os.environ.get("SALUTEBOT_DB", "salute-bot.db")

    logger.info("Database: %s", db_path)

    try:
        config = EnvConfig()
        crypto = Crypto.from_env(config)

        logger.info("Encryption configuration loaded")

        store = Store(db_path, crypto)
        logger.info("Database opened")

        scraper = LiveScraper.from_env()
        logger.info("LiveScraper initialized")

        alerter = TelegramSender.from_env()
        logger.info("Alerter initialized")

        logger.info("Starting daemon loop")

        run(store, scraper, alerter)

    except KeyboardInterrupt:
        logger.info("Watcher stopped by user")

    except Exception:
        logger.exception("Watcher crashed")
        raise


if __name__ == "__main__":
    main()