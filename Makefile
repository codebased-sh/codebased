
.gitignore:
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore > .gitignore
	curl -s https://raw.githubusercontent.com/github/gitignore/main/Global/JetBrains.gitignore >> .gitignore

.PHONY: lfg

lfg:
	git add -A
	git commit -m "Ship it"
	git push origin master
	@current_gbp=$$(cat GBPs.txt 2>/dev/null || echo 0); \
	new_points=$$(od -An -N2 -i /dev/urandom | tr -d ' '); \
	new_total=$$((current_gbp + new_points)); \
	echo $$new_total > GBPs.txt; \
	echo "Great job, Max! You earned $$new_points good boy points. Your new total is $$new_total."

package-build:
	poetry build

package-publish: package-build
	poetry publish

reset:
	rm -rf ~/.codebased

manual-test:
	codebased
	codebased -n 1 "Curses TermUI"
	codebased --root ../codebased
	codebased --root codebased/
	codebased --root ~/dev/oss/ripgrep