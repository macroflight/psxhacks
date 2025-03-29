# Note: needs e.g apt-get install python3-venv

LINTVENVDIR = $${HOME}/.venv-lint/$(osname)

LINTFILES = radiosync.py frankenusb.py comparator.py psx_fuel_transfer.py psx_shutdown.py show_psx.py show_usb.py frankenwind.py frankenfreeze.py make_gatefinder_database.py
CONFIGFILES = frankenusb-frankensim.conf

osname=$(shell uname -s)-$(shell uname -r)

lint: venv pylint configlint pycodestyle pydocstyle
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

clean:
	$(info * LINT: Removing venv)
	rm -rf $(LINTVENVDIR)
