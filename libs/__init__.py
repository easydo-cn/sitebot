import sys
from .digital_signature import sign, verify

if sys.platform.startswith('win'):
    from .win_app_config import (get_all_program, set_user_editor)
