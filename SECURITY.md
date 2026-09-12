# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
private vulnerability reporting (Security → Report a vulnerability) on this
repository, or contact a maintainer directly.

We aim to acknowledge a report within a few days and to ship a fix or a
mitigation as soon as it is practical. Credit is given in the release notes
unless you prefer to stay anonymous.

## What this software does, and the risks that come with it

Kairos Code runs an autonomous loop that **executes model-generated actions**
inside your project: it writes files and, depending on your configuration, runs
terminal commands. Treat it like running a script you have not read.

* **Run it on a machine (or container) you are willing to expose.** Prefer the
  Docker setup or a VM for anything you do not fully trust. The tool sandbox
  (`kairos/sandbox.py`) offers several levels — be aware that the default is the
  most permissive one.
* **Do not expose the HTTP API to the internet.** The API can drive agents and
  read project files. If you must reach it remotely, put it behind a reverse
  proxy with authentication. Closing an API-key-less instance to
  `127.0.0.1` (the default) is intentional.
* **Provider keys are yours.** They are read from `data/settings.json` or the
  environment, never sent anywhere except your configured provider. Do not commit
  them; do not paste them into issues.
* **Prompt injection is in scope.** Content the agent reads (files, web pages,
  command output) can try to steer it. Reports that show a bypass of the review
  gate, the sandbox, the permission rules, or the approval modes are the most
  valuable kind we can receive.

## Supported versions

The project is pre-1.0: fixes land on `main` and are released as the next 0.1.x.
Please reproduce against the latest `main` before reporting.
