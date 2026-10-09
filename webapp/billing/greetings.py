"""Optional greetings; personal messages belong in local practice settings."""
from random import choice

MESSAGES = (
    "Ich hoffe, du hast einen schönen Tag!",
    "Lass dich nicht unterkriegen!",
    "Du schaffst das!",
    "Nimm dir auch mal wieder ein bisschen Zeit nur für dich!",
    "Fühle dich gedrückt!",
    "Grüße an die Füße!",
)


def random_greeting(custom_messages=""):
    messages = [line.strip() for line in custom_messages.splitlines() if line.strip()]
    return choice(messages or MESSAGES)
