import logging
import os

from flask import Flask

from Config import cache
from Fuction import run_alembic_upgrade
from apps.api import api_app
from apps.dandan.app import dandan
from apps.danmu.app import danmu
from apps.fnos.app import fnos_app
from core.db.db import SessionLocal
from core.db.model import Base
from core.db.model import videoConfigDb, recordDb

app = Flask(__name__)
# 配置内存缓存
app.config['CACHE_TYPE'] = 'FileSystemCache'  # 文件缓存
app.config['CACHE_DIR'] = 'data/cache_dir'       # 缓存文件存放目录
app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # 默认 5 分钟

cache.init_app(app)
app.logger.setLevel(logging.INFO)

app.register_blueprint(danmu, url_prefix='/danmu')
app.register_blueprint(fnos_app, url_prefix='/fnos')
app.register_blueprint(api_app, url_prefix='/api')
app.register_blueprint(dandan)


@app.teardown_appcontext
def release_database_session(error=None):
    SessionLocal.remove()

if not os.path.exists('./data/log'):
    os.makedirs('./data/log')

# 执行数据库迁移操作
try:
    run_alembic_upgrade()
    print("Database migration completed successfully")
except Exception as e:
    raise RuntimeError('Database migration failed; restore or repair the database before startup') from e

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', debug=debug_mode)
