# Setup guide

Install the dependencies with the package manager of your choice, then copy the
example configuration file into place and adjust the database connection string
to point at your local instance. Start the web service first, wait until its
health check reports ready, and only then launch the background worker so that
the queue consumer finds every table it expects. When both services are up you
can open the dashboard in a browser and sign in with the bootstrap account that
the seed script printed to the console during the first start of the stack.
