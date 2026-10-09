from getpass import getpass
from werkzeug.security import generate_password_hash

password = getpass("Enter the administrator password: ")
print("\nGenerated hash:\n")
print(generate_password_hash(password))
