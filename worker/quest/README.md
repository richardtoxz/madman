Quest automation module

This package contains all quest-farming logic used by the worker process.

Environment variables:
- YES_CAPTCHA_API_KEY: optional. Enables automatic hCaptcha solving for quest reward claim requests.

Dependency notes:
- No new package is required beyond what already exists in requirements.txt.
- Core quest feature uses aiohttp, which is already listed.
