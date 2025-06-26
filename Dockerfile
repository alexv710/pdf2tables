FROM python:3.13-slim

COPY pyproject.toml .
COPY poetry.lock .
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev --no-interaction --no-ansi

COPY . .

# start the dash application
# the minimal command could also be poetry run python app.py
# but we use gunicorn for better performance in production
# gunicorn is a WSGI HTTP server for UNIX
# benefits of using gunicorn:
# - multiple worker processes to handle requests concurrently
# - better performance than the default Flask server
# - can be easily configured with different workers, threads, and timeouts
# - static file serving
# - Nginx can be used as a reverse proxy in front of gunicorn
# - load balancing multiple gunicorn workers
CMD gunicorn -b 0.0.0.0:80 app:server
