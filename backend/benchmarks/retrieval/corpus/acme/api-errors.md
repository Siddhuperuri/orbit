# API Error Reference

## ERR-4012 Access token expired

The access token presented is past its expiry. Obtain a new token with the refresh token and retry the request.

## ERR-4031 Insufficient scope

The token is valid but lacks the scope the endpoint requires. Request the scope listed in the endpoint documentation.

## ERR-4290 Rate limit exceeded

Each API key may make 600 requests per minute. Excess requests are rejected with this code and a Retry-After header giving the seconds to wait.

## ERR-5031 Upstream dependency timeout

A downstream service did not answer within its deadline. The request is safe to retry with exponential backoff.
