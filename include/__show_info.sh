function show_info()
{
msg_info "Username: " $(whoami)
echo ""
msg_warn "Network interfaces:" 
NET="ip -4 -o a | cut -d ' ' -f 2,7 | cut -d '/' -f 1"
eval $NET
echo ""
msg_warn "Public IP:" 
eval "dig +short myip.opendns.com @resolver1.opendns.com"
}