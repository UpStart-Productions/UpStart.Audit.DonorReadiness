#!/usr/bin/env bash
# setup_cloudwatch.sh — One-time CloudWatch monitoring setup for donor-readiness-audit Lambda
#
# Usage:
#   chmod +x setup_cloudwatch.sh
#   ./setup_cloudwatch.sh [alert-email]
#
# Defaults to chiefupstart@gmail.com if no email is provided.
# Requires AWS CLI configured with credentials that have CloudWatch, SNS, and logs permissions.

set -e

REGION="us-west-2"
FUNCTION="donor-readiness-audit"
LOG_GROUP="/aws/lambda/${FUNCTION}"
ALERT_EMAIL="${1:-chiefupstart@gmail.com}"
DASHBOARD_NAME="DonorAudit"

echo ""
echo "========================================================"
echo "  CloudWatch setup: ${FUNCTION}"
echo "  Region:  ${REGION}"
echo "  Alerts → ${ALERT_EMAIL}"
echo "========================================================"
echo ""

# ── 1. SNS alert topic ─────────────────────────────────────────────────────────
echo "[1/6] Creating SNS alert topic..."
TOPIC_ARN=$(aws sns create-topic \
  --name donor-audit-alerts \
  --region "${REGION}" \
  --query TopicArn \
  --output text)
echo "      Topic ARN: ${TOPIC_ARN}"

# ── 2. Email subscription ──────────────────────────────────────────────────────
echo "[2/6] Subscribing ${ALERT_EMAIL} to alerts..."
aws sns subscribe \
  --topic-arn "${TOPIC_ARN}" \
  --protocol email \
  --notification-endpoint "${ALERT_EMAIL}" \
  --region "${REGION}" > /dev/null
echo "      ✓ Check your inbox — you must click Confirm Subscription before alerts arrive"

# ── 3. Alarm: any Lambda error ─────────────────────────────────────────────────
echo "[3/6] Creating error alarm..."
aws cloudwatch put-metric-alarm \
  --alarm-name "donor-audit-errors" \
  --alarm-description "donor-readiness-audit: Lambda error (exception or crash)" \
  --namespace "AWS/Lambda" \
  --metric-name "Errors" \
  --dimensions "Name=FunctionName,Value=${FUNCTION}" \
  --statistic "Sum" \
  --period 60 \
  --threshold 1 \
  --comparison-operator "GreaterThanOrEqualToThreshold" \
  --evaluation-periods 1 \
  --alarm-actions "${TOPIC_ARN}" \
  --treat-missing-data "notBreaching" \
  --region "${REGION}"
echo "      ✓ Alarm: any error → email"

# ── 4. Alarm: near-timeout (>8.5 min) ─────────────────────────────────────────
echo "[4/6] Creating near-timeout alarm..."
aws cloudwatch put-metric-alarm \
  --alarm-name "donor-audit-near-timeout" \
  --alarm-description "donor-readiness-audit: run exceeded 8.5 min — approaching 10-min Lambda limit" \
  --namespace "AWS/Lambda" \
  --metric-name "Duration" \
  --dimensions "Name=FunctionName,Value=${FUNCTION}" \
  --statistic "Maximum" \
  --period 60 \
  --threshold 510000 \
  --comparison-operator "GreaterThanOrEqualToThreshold" \
  --evaluation-periods 1 \
  --alarm-actions "${TOPIC_ARN}" \
  --treat-missing-data "notBreaching" \
  --region "${REGION}"
echo "      ✓ Alarm: duration > 8.5 min → email"

# ── 5. Saved Log Insights query ────────────────────────────────────────────────
echo "[5/6] Saving Log Insights query..."
aws logs put-query-definition \
  --name "Donor Audit — Run History" \
  --log-group-names "${LOG_GROUP}" \
  --query-string "fields @timestamp, event, url, org, duration_s, error | filter ispresent(event) | sort @timestamp desc | limit 50" \
  --region "${REGION}" > /dev/null
echo "      ✓ Saved query: 'Donor Audit — Run History'"

# ── 6. CloudWatch dashboard ────────────────────────────────────────────────────
echo "[6/6] Creating CloudWatch dashboard..."

DASHBOARD_BODY=$(cat <<EOF
{
  "widgets": [
    {
      "type": "metric",
      "x": 0, "y": 0, "width": 8, "height": 6,
      "properties": {
        "title": "Invocations",
        "region": "${REGION}",
        "metrics": [["AWS/Lambda", "Invocations", "FunctionName", "${FUNCTION}"]],
        "stat": "Sum",
        "period": 3600,
        "view": "timeSeries"
      }
    },
    {
      "type": "metric",
      "x": 8, "y": 0, "width": 8, "height": 6,
      "properties": {
        "title": "Errors",
        "region": "${REGION}",
        "metrics": [["AWS/Lambda", "Errors", "FunctionName", "${FUNCTION}"]],
        "stat": "Sum",
        "period": 3600,
        "view": "timeSeries",
        "annotations": {
          "horizontal": [{"value": 1, "label": "Any error", "color": "#d62728"}]
        }
      }
    },
    {
      "type": "metric",
      "x": 16, "y": 0, "width": 8, "height": 6,
      "properties": {
        "title": "Duration",
        "region": "${REGION}",
        "metrics": [
          ["AWS/Lambda", "Duration", "FunctionName", "${FUNCTION}", {"stat": "p50",     "label": "p50"}],
          ["AWS/Lambda", "Duration", "FunctionName", "${FUNCTION}", {"stat": "p99",     "label": "p99"}],
          ["AWS/Lambda", "Duration", "FunctionName", "${FUNCTION}", {"stat": "Maximum", "label": "Max"}]
        ],
        "period": 3600,
        "view": "timeSeries",
        "annotations": {
          "horizontal": [{"value": 510000, "label": "8.5 min warning", "color": "#ff7f0e"}]
        }
      }
    },
    {
      "type": "log",
      "x": 0, "y": 6, "width": 24, "height": 8,
      "properties": {
        "title": "Audit Run History",
        "region": "${REGION}",
        "query": "SOURCE '${LOG_GROUP}' | fields @timestamp, event, url, org, duration_s, error | filter ispresent(event) | sort @timestamp desc | limit 20",
        "view": "table"
      }
    }
  ]
}
EOF
)

aws cloudwatch put-dashboard \
  --dashboard-name "${DASHBOARD_NAME}" \
  --dashboard-body "${DASHBOARD_BODY}" \
  --region "${REGION}" > /dev/null
echo "      ✓ Dashboard created"

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "  All done. Here's what was set up:"
echo ""
echo "  ALERTS  → ${ALERT_EMAIL}"
echo "    • Any Lambda error"
echo "    • Run time > 8.5 min (before 10-min timeout)"
echo "    (check inbox and confirm the SNS subscription)"
echo ""
echo "  DASHBOARD"
echo "  https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#dashboards:name=${DASHBOARD_NAME}"
echo ""
echo "  LOG INSIGHTS — Audit Run History"
echo "  https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#logsV2:logs-insights"
echo "  (select saved query 'Donor Audit — Run History')"
echo ""
echo "  RAW LOGS"
echo "  https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#logsV2:log-groups/log-group/\$252Faws\$252Flambda\$252F${FUNCTION}"
echo "========================================================"
echo ""
