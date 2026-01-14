-- List all distinct mail status values in the mails table
SELECT DISTINCT status FROM mails;

-- Update any uppercase or mixed case statuses to lowercase
UPDATE mails SET status = LOWER(status) WHERE BINARY status != LOWER(status);
