# Telegram Premium Bot

This is a Telegram bot for automating Premium subscription purchases, supporting crypto (TON/TRC20) and OkPay payments.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/xf5201/xgacf.git
    cd xgacf
    ```

2.  **Install dependencies:**
    ```bash
    # Create virtual environment
    python3 -m venv venv
    source venv/bin/activate
    # Install Python packages
    pip install -r requirements.txt
    ```

3.  **Configuration:**
    Copy `.env.example` to `.env` and fill in your details:
    ```bash
    cp .env.example .env
    # Edit .env file
    ```
    **Crucial:** You need to obtain the `FRAGMENT_COOKIE` and `FRAGMENT_HASH` by monitoring network traffic when manually purchasing a gift on Fragment.

4.  **Run:**
    ```bash
    bash start.sh
    ```

## Notes

*   The bot includes a simple web server for OkPay callbacks (port 8080). You need to use a reverse proxy or a tool like `cpolar` (included in the repo) to expose it publicly.
*   The project uses SQLite (`orders.db`) for order storage.
*   For any issues, contact @xgacf on Telegram.
