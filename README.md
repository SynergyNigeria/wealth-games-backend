# MR Wealth’s Giveaway — backend

Django REST API and admin for MR Wealth’s Giveaway. This folder is the root of
the backend GitHub repository; it can be pushed and hosted independently of the
frontend.

## Local development

Requires Python 3.10+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8001
```

The API is available at `http://127.0.0.1:8001/api/`, and Django admin is at
`http://127.0.0.1:8001/admin/`.

Run tests with:

```powershell
python manage.py test
```

## Deploy to PythonAnywhere

1. Push the contents of this folder to its own GitHub repository.
2. In a PythonAnywhere Bash console, clone that repository into your home
   directory. Create a virtualenv using Python 3.10 or newer and install
   dependencies (use the same Python version when creating the web app):

   ```bash
   mkvirtualenv --python=/usr/bin/python3.12 giveaway
   pip install -r ~/your-backend-repository/requirements.txt
   ```

3. Create a PythonAnywhere web app for the same Python version. Set its
   **Virtualenv** field to the environment where you installed the requirements.
4. Edit the web app's WSGI configuration file. Set the project path and
   deployment environment there, then load Django's WSGI application. Replace
   the example values; keep the secret private and do not commit them:

   ```python
   import os
   import sys

   project_path = "/home/yourusername/your-backend-repository"
   if project_path not in sys.path:
       sys.path.insert(0, project_path)

   os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
   os.environ["DJANGO_SECRET_KEY"] = "replace-with-a-long-random-secret"
   os.environ["DJANGO_DEBUG"] = "False"
   os.environ["DJANGO_ALLOWED_HOSTS"] = "yourusername.pythonanywhere.com"
   os.environ["CORS_ALLOWED_ORIGINS"] = "https://your-app.onrender.com"

   from django.core.wsgi import get_wsgi_application

   application = get_wsgi_application()
   ```

   Generate a secret locally with
   `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`.
   `DJANGO_ALLOWED_HOSTS` takes hostnames without a scheme;
   `CORS_ALLOWED_ORIGINS` takes full frontend origins (comma-separated if
   there is more than one).
5. In a Bash console using the same virtualenv, run migrations and collect
   Django admin static files:

   ```bash
   cd ~/your-backend-repository
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py createsuperuser
   ```

6. In the web app's **Static files** section, map URL `/static/` to
   `/home/yourusername/your-backend-repository/staticfiles/`. Reload the web
   app, then check `https://yourusername.pythonanywhere.com/api/health/`.

The SQLite database is intentionally excluded from Git. Migrations create a
fresh database on the server; create the admin account there. Keep the
repository and database on PythonAnywhere's persistent home directory. Back up
the database regularly, and never replace it with a local development copy.
