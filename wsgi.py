from index import create_app, setup_bot
from waitress import serve

app = create_app()
setup_bot()

if __name__ == "__main__":
    serve(app, host='0.0.0.0', port=8080)
