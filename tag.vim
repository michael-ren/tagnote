command -nargs=* WriteAsDate :call WriteAsDate(<f-args>)

let NOTES_DIRECTORY = $HOME . "/notes"

function WriteAsDate(...)
  let filename = strftime("%F_%H-%M-%S.txt")
  let tags = ""
  for arg in a:000
    let tags = l:tags . " " . shellescape(l:arg)
  endfor
  execute 'write ' . g:NOTES_DIRECTORY . "/" . l:filename
  echom system('tag -r :0 add ' . l:filename . l:tags)
endfunction

