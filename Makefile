
.gitignore:
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore > .gitignore
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Global/JetBrains.gitignore >> .gitignore

.PHONY: lfg

lfg:
	git add -A
	git commit -m "Ship it"
	git push origin master
