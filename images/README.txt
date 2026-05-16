Place favicons / app icons here. The HTML expects:

    favicon-16x16.png
    favicon-32x32.png
    apple-touch-icon.png   (180x180)

Until real assets are provided, the browser will fall back silently — the
backend mounts this directory at `/images/` only if it exists.
