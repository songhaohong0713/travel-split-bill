function labelFor(member) {
  if (member && member.is_current) return "我"
  const nickname = String(member && member.nickname || "").trim()
  return nickname || "同行人"
}

function memberChoices(members) {
  return (members || []).map((member) => ({ id: String(member.id), label: labelFor(member) }))
}

function memberLabelMap(members) {
  return Object.fromEntries(memberChoices(members).map((member) => [member.id, member.label]))
}

function memberLabel(options, id) {
  const member = (options || []).find((option) => option.id === id)
  return member ? member.label : "同行人"
}

module.exports = { memberChoices, memberLabel, memberLabelMap }
