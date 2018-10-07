command -nargs=* WriteAsDate :call WriteAsDate(<f-args>)

let NOTES_DIRECTORY = $HOME . '/notes'

let UTC = 0

function WriteAsDate(...)
  if g:UTC
    let filename = system('date -u +%F_%H-%M-%S.txt')
    if v:shell_error
      echo "Could not get timestamp\n\n" . filename
      return
    endif
  else
    let filename = strftime('%F_%H-%M-%S.txt')
  endif
  let tags = ''
  for arg in a:000
    let tags = l:tags . ' ' . shellescape(l:arg)
  endfor
  execute 'saveas ' . g:NOTES_DIRECTORY . '/' . l:filename
  let result = system('tag -r :0 add ' . l:filename . l:tags)
  if v:shell_error
    echo "Could not add note\n\n" . result
    return
  endif
endfunction

