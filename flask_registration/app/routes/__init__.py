from app.routes.main import main
from app.routes.auth import auth
from app.routes.forum import forum_bp, _seed_forum
from app.routes.profile import profile_bp
from app.routes.admin import admin_bp
from app.routes.social import social

__all__ = ['main', 'auth', 'forum_bp', 'profile_bp', 'admin_bp', 'social', '_seed_forum']
