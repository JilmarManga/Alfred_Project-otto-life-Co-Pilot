USER_CONTEXT = {}


def get_user_context(user_id: str) -> dict:
	if user_id not in USER_CONTEXT:
		USER_CONTEXT[user_id] = {}
	return USER_CONTEXT[user_id]

def update_user_context(user_id: str, key: str, value):
    if user_id not in USER_CONTEXT:
        USER_CONTEXT[user_id] = {}
    USER_CONTEXT[user_id][key] = value
