.gitignore:
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore > .gitignore
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Global/JetBrains.gitignore >> .gitignore

package-build:
	poetry build

package-publish: package-build
	poetry publish

test:
	poetry run pytest