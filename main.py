from logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


def main():
    logger.info("Hello from spotinotifs", extra={"event": "main_started"})


if __name__ == "__main__":
    main()
