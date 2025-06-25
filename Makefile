# Note: needs e.g apt-get install python3-venv

LINTVENVDIR = $${HOME}/.venv-lint/$(osname)

# Explicitly list the scripts that are part of the repo and supposed
# to pass lint. There's usually some other stuff lying around as
# well.
LINTFILES = \
	show_psx.py \
	show_usb.py \
	frankenusb.py \
	frankenwind.py \
	frankenfreeze.py \
	router/*.py \
	psx_fuel_transfer.py \
	psx_shutdown.py \
	radiosync.py \
	comparator.py \
	make_gatefinder_database.py \
	psx_msfs_sync_checker.py

CONFIGFILES = config_examples/*

MARKDOWNFILES = router/*.md

osname=$(shell uname -s)-$(shell uname -r)

lint: venv markdownlint pylint configlint pycodestyle pydocstyle
	$(info LINT: Your code passed lint!)

venv: $(LINTVENVDIR)/bin/activate

$(LINTVENVDIR)/bin/activate:
	$(info * LINT: Trying to setup a Python3 venv to install lint tools in)
	test -d $(LINTVENVDIR) || (python3 -m venv $(LINTVENVDIR); . $(LINTVENVDIR)/bin/activate; pip install pylint pycodestyle pydocstyle)
	touch $(LINTVENVDIR)/bin/activate

pylint: venv
	$(info * LINT: Running pylint)
	. $(LINTVENVDIR)/bin/activate; pylint --rcfile=.pylintrc.toml -r n $(LINTFILES)

configlint: venv
	$(info * LINT: Running pylint on config files)
	. $(LINTVENVDIR)/bin/activate; pylint --rcfile=.pylintrc.toml --disable=duplicate-code -r n $(CONFIGFILES)

pycodestyle: venv
	$(info * LINT: Running pycodestyle)
	. $(LINTVENVDIR)/bin/activate; pycodestyle --max-line-length=99999 --ignore=W504,E722 $(LINTFILES)

pydocstyle: venv
	$(info * LINT: Running pydocstyle)
	. $(LINTVENVDIR)/bin/activate; pydocstyle --ignore=D104,D203,D213 $(LINTFILES)

markdownlint:
	$(info * LINT: Running markdownlint)
	mdl --style=.mdl.rb $(MARKDOWNFILES)

clean:
	$(info * LINT: Removing venv)
	rm -rf $(LINTVENVDIR)
