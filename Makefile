# Note: needs e.g apt-get install python3-venv

#export PATH := /home/kronberg/pkg/python/3.13.5/bin:/home/kronberg/pkg/python/3.13.4/bin:$(PATH)
export PATH := /home/kronberg/pkg/python/latest/bin:$(PATH)

LINTVENVDIR = $${HOME}/.venv-lint-psxhacks/$(osname)

# Run pylint on all *_.py files except psx.py (which is not included
# in the repo but frequenly there anyway for testing.
LINTFILES = $(shell find . -name '*.py' | egrep -v '(psx.py|test_latency)')

PYCODESTYLEFILES = $(shell find . -name '*.py' | egrep -v '(psx.py|test_latency)')

PYDOCSTYLEFILES = $(shell find . -name '*.py' | egrep -v '(psx.py|test_latency|frankentow)')


CONFIGFILES = config_examples/*

MARKDOWNFILES = router/*.md

TOMLFILES = router/config_examples/*.toml

osname=$(shell uname -s)-$(shell uname -r)

lint: venv unittests tomlcheck markdownlint pylint configlint pycodestyle pydocstyle
	$(info LINT: Your code passed lint!)

venv: $(LINTVENVDIR)/bin/activate

$(LINTVENVDIR)/bin/activate:
	$(info * LINT: Trying to setup a Python3 venv to install lint tools in)
	test -d $(LINTVENVDIR) || (python3 -m venv $(LINTVENVDIR); . $(LINTVENVDIR)/bin/activate; pip install pylint pycodestyle pydocstyle pyproj tomlcheck)
	touch $(LINTVENVDIR)/bin/activate

pylint: venv
	$(info * LINT: Running pylint on scripts)
	. $(LINTVENVDIR)/bin/activate; pylint --rcfile=.pylintrc.toml -r n $(LINTFILES)

configlint: venv
	$(info * LINT: Running pylint on Python format config files)
	. $(LINTVENVDIR)/bin/activate; pylint --rcfile=.pylintrc.toml --disable=duplicate-code -r n $(CONFIGFILES)

pycodestyle: venv
	$(info * LINT: Running pycodestyle)
	. $(LINTVENVDIR)/bin/activate; pycodestyle --max-line-length=99999 --ignore=W504,E722 $(PYCODESTYLEFILES)

pydocstyle: venv
	$(info * LINT: Running pydocstyle)
	. $(LINTVENVDIR)/bin/activate; pydocstyle --ignore=D104,D203,D213 $(PYDOCSTYLEFILES)

markdownlint:
	$(info * LINT: Running markdownlint)
	mdl --style=.mdl.rb $(MARKDOWNFILES)

tomlcheck:
	$(info * LINT: Running tomlcheck)
	. $(LINTVENVDIR)/bin/activate; tomlcheck $(TOMLFILES)

unittests:
	$(info * LINT: Running unit tests)
	. $(LINTVENVDIR)/bin/activate; python3 -m unittest -v router/frankenrouter/*.py

pslint:
	$(info * LINT: Running PSScriptAnalyzer on PowerShell scripts)
	@command -v pwsh >/dev/null 2>&1 || { echo "pwsh not found - install PowerShell Core and run: pwsh -Command \"Install-Module PSScriptAnalyzer -Scope CurrentUser -Force\""; exit 1; }
	pwsh -NoProfile -Command "$$r = Invoke-ScriptAnalyzer -Path ./start_scripts/ -Recurse -Severity Error,Warning; $$r | Select-Object ScriptName,Line,Severity,Message | Format-Table -AutoSize; exit $$r.Count"

clean:
	$(info * LINT: Removing venv)
	rm -rf $(LINTVENVDIR)
