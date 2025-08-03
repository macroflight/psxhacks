# Note: needs e.g apt-get install python3-venv

LINTVENVDIR = $${HOME}/.venv-lint-psxhacks/$(osname)

# Run pylint on all *_.py files except psx.py (which is not included
# in the repo but frequenly there anyway for testing.
LINTFILES = $(shell find . -name '*.py' ! -name psx.py)

PYCODESTYLEFILES = $(shell find . -name '*.py' ! -name psx.py)

PYDOCSTYLEFILES = $(shell find . -name '*.py' | egrep -v '(psx.py|frankentow)')


CONFIGFILES = config_examples/*

MARKDOWNFILES = router/*.md

TOMLFILES = router/config_examples/*.toml

osname=$(shell uname -s)-$(shell uname -r)

lint: venv unittests tomlcheck markdownlint pylint configlint pycodestyle pydocstyle
	$(info LINT: Your code passed lint!)

venv: $(LINTVENVDIR)/bin/activate

$(LINTVENVDIR)/bin/activate:
	$(info * LINT: Trying to setup a Python3 venv to install lint tools in)
	test -d $(LINTVENVDIR) || (python3 -m venv $(LINTVENVDIR); . $(LINTVENVDIR)/bin/activate; pip install pylint pycodestyle pydocstyle pyproj)
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
	tomlcheck $(TOMLFILES)

unittests:
	$(info * LINT: Running unit tests)
	python -m unittest -v router/frankenrouter/*.py

clean:
	$(info * LINT: Removing venv)
	rm -rf $(LINTVENVDIR)
