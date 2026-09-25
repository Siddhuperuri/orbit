"""The document processing pipeline's domain: what a parsed document is, what a
chunk is, how a job moves through its states, and how a failure is classified.

Everything here is pure -- no I/O, no framework, no clock read. The stages that
do I/O (fetching, parsing libraries, embedding providers, the database) are
ports implemented in `infrastructure`; the orchestration is in
`application/processing`.
"""
