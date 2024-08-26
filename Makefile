
.gitignore:
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore > .gitignore
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Global/JetBrains.gitignore >> .gitignore

.PHONY: lfg

lfg:
	git add -A
	git commit -m "Ship it"
	git push origin master
	echo "Great job, Max! Here's $$(od -An -N2 -i /dev/urandom | tr -d ' ') good boy points."

package-build:
	poetry build

package-publish: package-build
	poetry publish

reset:
	rm -rf ~/.codebased