function formatBalance(amount) {
  return `$${amount.toFixed(2)}`;
}

function roundCents(amount) {
  return Math.round(amount * 100) / 100;
}

module.exports = { formatBalance, roundCents };
