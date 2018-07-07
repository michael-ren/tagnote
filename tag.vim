command -nargs=* WriteAsDate :call WriteAsDate(<f-args>)

let NOTES_DIRECTORY = $HOME . "/notes"

let UTC = 0

function WriteAsDate(...)
  if g:UTC
    let filename = system('date -u +%F_%H-%M-%S.txt')
  else
    let filename = strftime("%F_%H-%M-%S.txt")
  endif
  let tags = ""
  for arg in a:000
    let tags = l:tags . " " . shellescape(l:arg)
  endfor
  execute 'write ' . g:NOTES_DIRECTORY . "/" . l:filename
  echom system('tag -r :0 add ' . l:filename . l:tags)
endfunction

