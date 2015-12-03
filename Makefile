#

#  Compile web-site assets
#
#  Largely this means concatenating and 'minifying' some javascript and css 
#  assets to reduce browser load time (fewer http requests). 
#

# Configuration options
#
# On most platforms: 
#PYVENV = pyvenv-3.4
# On ix (with bug in ubuntu)
PYVENV = pyvenv-3.4 --without-pip


#
#  The files we generate at build-time
# 
DERIVED = static/js/*.min.js
#  Can't install properly on shared machine: 
#  CSS-CLEAN = (cd static/js; node_modules/clean-css/bin/cleancss)

##
## Default recipe:  Rebuild whatever needs rebuilding.
## Note this is the default rule --- 'make' alone is same as 'make all'
##
all:	
	(cd static/js; make all)


##
## Install in a new environment:
##     We need to rebuild the Python environment to match
##     Everything is straightforward EXCEPT that we need 
##     to work around an ubuntu bug in pyvenv on ix
##     
install:
	# pyvenv-3.4 env ### BUGGY on ix
	$(PYVENV)  env
	make env/bin/pip
	(.  env/bin/activate; pip install -r requirements.txt)

env/bin/pip: env/bin/activate
	echo ""
	(.  env/bin/activate; curl https://bootstrap.pypa.io/get-pip.py | python)


dist:
	pip freeze >requirements.txt



##
## Make a clean start 
##
clean:	
	rm -f $(DERIVED)

##
## Recipes for components 
## 


#  I haven't been able to make this work on ix: 
## Concatenate and minify CSS files with cleancss
# %.min.css:	%.css
#	$(CSS-CLEAN) $<  > $@

## Combine and minify javascript files with browserify
%.min.js:	%.js
	$(BROWSERIFY) $< > $@

## Special case for top-level javascript file
static/js/busy.min.js:  static/js/busy.js
	$(BROWSERIFY) $< --standalone busy > $@
