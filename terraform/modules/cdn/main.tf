# --- S3 Landing Bucket ---
resource "aws_s3_bucket" "landing" {
  bucket = "ortobahn-landing"
}

resource "aws_s3_bucket_policy" "landing" {
  bucket = aws_s3_bucket.landing.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontServicePrincipal"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.landing.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = "arn:aws:cloudfront::418295677815:distribution/E1R6PE83G6T984"
          }
        }
      }
    ]
  })
}

# --- Origin Access Control ---
resource "aws_cloudfront_origin_access_control" "landing" {
  name                              = "ortobahn-landing-oac"
  description                       = "OAC for ortobahn landing page"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# --- CloudFront Function ---
resource "aws_cloudfront_function" "redirect" {
  name    = "ortobahn-redirect"
  runtime = "cloudfront-js-2.0"
  comment = "URL redirect handler"
  publish = true
  code    = <<-EOF
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri.endsWith('/') && uri !== '/') {
        return {
          statusCode: 301,
          statusDescription: 'Moved Permanently',
          headers: { location: { value: uri.slice(0, -1) } }
        };
      }
      if (!uri.includes('.') && uri !== '/') {
        request.uri = uri + '.html';
      } else if (uri === '/') {
        request.uri = '/index.html';
      }
      return request;
    }
  EOF

  lifecycle {
    ignore_changes = [code]
  }
}

# CloudFront distribution is read via data source — too complex to manage
# (multiple origins including API Gateway) without risking disruption.
# The S3 bucket, OAC, and function are managed as resources above.
data "aws_cloudfront_distribution" "landing" {
  id = "E1R6PE83G6T984"
}
