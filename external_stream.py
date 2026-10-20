"""Dedicated streaming workers, isolated from ordinary fnos-tv API workers."""
from flask import Flask, Blueprint
from apps.danmu.external_play import register_external_routes

app = Flask(__name__)
routes = Blueprint('external_stream', __name__, url_prefix='/danmu')
register_external_routes(routes)
app.register_blueprint(routes)
